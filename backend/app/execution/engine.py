from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from ..audit import append_trace_to_payload, load_last_event_hash
from ..models import MemoryRecord, NodeRunRecord, RunEventRecord, RunRecord
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
from .simulation_state import (
    derive_state_memory,
    init_simulation_state,
    merge_world_state,
    normalize_world_state,
    safe_float,
    update_distributed_state,
    update_simulation_state,
)


class WorkflowValidationError(ValueError):
    pass


class WorkflowEngine:
    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self.provider = provider

    @staticmethod
    def _validate(workflow: WorkflowDefinition) -> None:
        node_ids = {n.id for n in workflow.nodes}
        if len(node_ids) != len(workflow.nodes):
            raise WorkflowValidationError("duplicate node id found")
        for edge in workflow.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise WorkflowValidationError(f"edge {edge.id} references unknown node")
        for entry in workflow.entry_nodes:
            if entry not in node_ids:
                raise WorkflowValidationError(f"entry node '{entry}' does not exist")
        simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
        continuous = str(simulation.get("execution_model", "")).strip().lower() == "continuous"
        dynamic_config = simulation.get("dynamic_state", {})
        dynamic_enabled = not isinstance(dynamic_config, dict) or bool(dynamic_config.get("enabled", True))
        if continuous and dynamic_enabled and not any(node.type == "director" for node in workflow.nodes):
            raise WorkflowValidationError("continuous dynamic state requires a Director node")
        WorkflowEngine._topological_sort(workflow.nodes, workflow.edges)

    @staticmethod
    def _topological_sort(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[str]:
        node_ids = [n.id for n in nodes]
        indegree: dict[str, int] = {nid: 0 for nid in node_ids}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            outgoing[edge.source].append(edge.target)
            indegree[edge.target] += 1

        q = deque([nid for nid in node_ids if indegree[nid] == 0])
        ordered: list[str] = []
        while q:
            cur = q.popleft()
            ordered.append(cur)
            for nxt in outgoing[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    q.append(nxt)
        if len(ordered) != len(node_ids):
            raise WorkflowValidationError("workflow contains cycle")
        return ordered

    @staticmethod
    def _should_follow_condition(edge: WorkflowEdge, source_output: dict[str, Any]) -> bool:
        if edge.condition is None:
            return True
        raw = str(edge.condition).strip().lower()
        expected = raw in {"true", "1", "yes", "y"}
        actual = bool(source_output.get("result", False))
        return actual == expected

    @staticmethod
    def _build_interaction_message(edge: WorkflowEdge, source_output: dict[str, Any]) -> dict[str, Any]:
        interaction = edge.interaction
        mode = interaction.mode if interaction else "dialogue"
        relation = interaction.relation if interaction else "peer"
        template = interaction.template if interaction else None
        intensity = interaction.intensity if interaction else 1.0

        readable_output = output_text(source_output) or str(
            source_output.get("global_guidance") or source_output.get("response") or source_output.get("result") or ""
        ).strip()
        if not readable_output:
            readable_output = "Output completed."

        if template:
            message = (
                template.replace("{{source}}", edge.source)
                .replace("{{target}}", edge.target)
                .replace("{{source_output}}", readable_output)
            )
        else:
            if mode == "report":
                message = f"[REPORT] {edge.source} -> {edge.target}: {readable_output}"
            elif mode == "instruction":
                message = f"[INSTRUCTION] {edge.source} requests action from {edge.target}: {readable_output}"
            elif mode == "feedback":
                message = f"[FEEDBACK] {edge.source} feedback to {edge.target}: {readable_output}"
            elif mode == "handoff":
                message = f"[HANDOFF] {edge.source} hands work to {edge.target}: {readable_output}"
            else:
                message = f"[DIALOGUE] {edge.source} to {edge.target}: {readable_output}"

        return {
            "source": edge.source,
            "target": edge.target,
            "mode": mode,
            "relation": relation,
            "message": message,
            "intensity": intensity,
            "payload": source_output,
        }

    @staticmethod
    def _descendants(start: str, edges: list[WorkflowEdge]) -> set[str]:
        graph: dict[str, list[str]] = defaultdict(list)
        for e in edges:
            graph[e.source].append(e.target)
        q = deque([start])
        seen: set[str] = set()
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in graph[cur]:
                q.append(nxt)
        return seen

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _redact_secrets(value: Any) -> Any:
        secret_keys = {"api_key", "authorization", "auth", "token", "secret", "password"}
        numeric_telemetry_keys = {
            "estimated_tokens",
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "token_count",
            "token_budget",
            "max_tokens",
            "max_output_tokens",
        }
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                key = str(k)
                normalized = key.lower()
                is_numeric_telemetry = normalized in numeric_telemetry_keys and isinstance(v, (int, float)) and not isinstance(v, bool)
                if not is_numeric_telemetry and (
                    normalized in secret_keys or any(s in normalized for s in ["api_key", "token", "secret", "password"])
                ):
                    out[key] = "***REDACTED***"
                else:
                    out[key] = WorkflowEngine._redact_secrets(v)
            return out
        if isinstance(value, list):
            return [WorkflowEngine._redact_secrets(v) for v in value]
        return value

    @staticmethod
    def _emit_event(
        db: Session,
        run_id: str,
        node_id: str,
        event: str,
        payload: dict[str, Any],
        *,
        duration_ms: int | None = None,
        trace_id: str | None = None,
        parent_event_ids: list[int] | None = None,
        caused_by: str = "engine",
        context_snapshot: dict[str, Any] | None = None,
        prev_hash: str = "",
    ) -> tuple[int, str]:
        now = datetime.now(timezone.utc)
        safe_payload = WorkflowEngine._redact_secrets(payload)
        signed_payload, event_hash = append_trace_to_payload(
            payload=safe_payload,
            run_id=run_id,
            node_id=node_id,
            event=event,
            timestamp=now,
            duration_ms=duration_ms,
            prev_hash=prev_hash,
            trace_id=trace_id,
            parent_event_ids=parent_event_ids,
            caused_by=caused_by,
            context_snapshot=context_snapshot,
        )
        rec = RunEventRecord(
            run_id=run_id,
            node_id=node_id,
            event=event,
            duration_ms=duration_ms,
            timestamp=now,
            payload_json=WorkflowEngine._serialize(signed_payload),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return int(rec.seq), event_hash

    @staticmethod
    def _load_previous_outputs(db: Session, run_id: str) -> dict[str, dict[str, Any]]:
        rows = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).all()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out[row.node_id] = json.loads(row.output_json)
        return out

    @staticmethod
    def _load_previous_outputs_until_event(db: Session, run_id: str, event_seq: int) -> dict[str, dict[str, Any]]:
        rows = (
            db.query(RunEventRecord)
            .filter(RunEventRecord.run_id == run_id, RunEventRecord.seq <= event_seq, RunEventRecord.event == "succeeded")
            .order_by(RunEventRecord.seq.asc())
            .all()
        )
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                payload = json.loads(row.payload_json)
            except Exception:  # noqa: BLE001
                payload = {}
            output = payload.get("output", {}) if isinstance(payload, dict) else {}
            if isinstance(output, dict):
                out[row.node_id] = output
        return out

    @staticmethod
    def _is_director_preflight_response(response: dict[str, Any]) -> bool:
        metadata = response.get("metadata", {})
        return isinstance(metadata, dict) and str(metadata.get("kind", "")) == "director_preflight_confirm"

    def _emit_human_resumed(
        self,
        *,
        db: Session,
        run_id: str,
        node_id: str,
        response: dict[str, Any],
        last_event_hash: str,
        parent_event_ids: list[int] | None = None,
    ) -> str:
        metadata = response.get("metadata", {})
        _, next_hash = self._emit_event(
            db,
            run_id,
            node_id,
            "human_resumed",
            {
                "responder": str(response.get("responder", "user")),
                "metadata": metadata if isinstance(metadata, dict) else {},
            },
            caused_by="human",
            parent_event_ids=parent_event_ids or [],
            context_snapshot={"responder": str(response.get("responder", "user")), "node_id": node_id},
            prev_hash=last_event_hash,
        )
        return next_hash

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

        execute_scope = self._descendants(request.retry_from_node, workflow.edges)
        execute_scope.add(request.retry_from_node)
        rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
        rollback_seq = rollback.get("event_seq") if isinstance(rollback, dict) else None
        if isinstance(rollback_seq, int):
            prev = self._load_previous_outputs_until_event(db, request.retry_from_run_id or run_id, rollback_seq)
        else:
            prev = self._load_previous_outputs(db, request.retry_from_run_id or run_id)
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
    def _load_recent_memories(db: Session, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = (
            db.query(MemoryRecord)
            .filter(MemoryRecord.workflow_id == workflow_id)
            .order_by(MemoryRecord.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                tags_raw = json.loads(row.tags_json)
            except Exception:  # noqa: BLE001
                tags_raw = []
            tags = tags_raw if isinstance(tags_raw, list) else []
            meta = tags_raw if isinstance(tags_raw, dict) else {}
            out.append(
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "node_id": row.node_id,
                    "role": row.role,
                    "content": row.content,
                    "tags": tags,
                    "kind": str(meta.get("kind", "fact") if isinstance(meta, dict) else "fact"),
                    "scope": str(meta.get("scope", "node") if isinstance(meta, dict) else "node"),
                    "subject": str(meta.get("subject", row.node_id) if isinstance(meta, dict) else row.node_id),
                    "importance": float(meta.get("importance", 0.5) if isinstance(meta, dict) else 0.5),
                    "confidence": float(meta.get("confidence", 0.7) if isinstance(meta, dict) else 0.7),
                    "source_event_seq": meta.get("source_event_seq") if isinstance(meta, dict) else None,
                    "state_before": meta.get("state_before", {}) if isinstance(meta, dict) else {},
                    "state_after": meta.get("state_after", {}) if isinstance(meta, dict) else {},
                    "trigger": str(meta.get("trigger", "") if isinstance(meta, dict) else ""),
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
            )
        return out

    @staticmethod
    def _memories_at_rollback(memories: list[dict[str, Any]], request: RunRequest) -> list[dict[str, Any]]:
        rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
        if not isinstance(rollback, dict):
            return memories
        source_run_id = str(rollback.get("from_run_id", "")).strip()
        try:
            event_seq = int(rollback.get("event_seq"))
        except (TypeError, ValueError):
            return memories
        filtered: list[dict[str, Any]] = []
        for memory in memories:
            if str(memory.get("run_id", "")) != source_run_id:
                filtered.append(memory)
                continue
            source_seq = memory.get("source_event_seq")
            if isinstance(source_seq, int) and source_seq <= event_seq:
                filtered.append(memory)
        return filtered

    @staticmethod
    def _extract_memory_content(node_type: str, output: dict[str, Any]) -> str:
        if node_type == "agent":
            return str(output.get("content", "")).strip()
        if node_type == "director":
            return str(output.get("global_guidance", "")).strip()
        if node_type == "external_agent":
            return str(output.get("text", "")).strip()
        if node_type == "prompt":
            return str(output.get("text", "")).strip()
        if node_type == "human_checkpoint":
            return str(output.get("response", "")).strip()
        return ""

    @staticmethod
    def _node_subject(node: WorkflowNode) -> str:
        return str(node.config.get("entity_name", node.config.get("role", node.id))).strip() or node.id

    @staticmethod
    def _memory_importance(node_type: str, status: str, output: dict[str, Any]) -> float:
        if status in {"failed", "waiting_human"}:
            return 0.9
        if node_type in {"director", "human_checkpoint"}:
            return 0.85
        text = json.dumps(output, ensure_ascii=False, default=str).lower()
        if any(k in text for k in ["final", "conclusion", "decision", "turning point", "结论", "决定", "转折"]):
            return 0.8
        return 0.55

    @staticmethod
    def _should_write_memory(
        *,
        kind: str,
        node: WorkflowNode,
        content: str,
        importance: float,
        state_before: dict[str, Any] | None = None,
        state_after: dict[str, Any] | None = None,
        interactions: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not content.strip():
            return False
        if node.type in {"condition", "tool"} and importance < 0.75:
            return False
        if kind == "state":
            return bool(state_after) and state_before != state_after
        if kind == "character":
            has_interaction = bool(interactions)
            return node.type in {"agent", "external_agent"} and (has_interaction or importance >= 0.6)
        if kind == "fact":
            if importance >= 0.75:
                return True
            text = content.lower()
            signal_words = [
                "decide",
                "decision",
                "conclude",
                "conflict",
                "agree",
                "reject",
                "turning point",
                "决定",
                "结论",
                "冲突",
                "同意",
                "拒绝",
                "转折",
            ]
            return bool(interactions) or any(word in text for word in signal_words) or len(content.strip()) >= 80
        return False

    @staticmethod
    def _memory_recall_score(item: dict[str, Any], node: WorkflowNode) -> float:
        subject = WorkflowEngine._node_subject(node)
        kind = str(item.get("kind", "fact"))
        scope = str(item.get("scope", "node"))
        node_id = str(item.get("node_id", ""))
        item_subject = str(item.get("subject", ""))
        importance = float(item.get("importance", 0.5) or 0.5)
        score = importance
        if scope in {"global", "environment"}:
            score += 0.45
        if node_id == node.id or item_subject == subject:
            score += 0.35
        if kind == "state":
            score += 0.3
        elif kind == "character":
            score += 0.2 if (node_id == node.id or item_subject == subject) else 0.05
        elif kind == "fact":
            score += 0.1
        if item.get("source_event_seq") is not None:
            score += 0.05
        return score

    @staticmethod
    def _memory_meta(
        *,
        kind: str,
        node: WorkflowNode,
        role: str,
        importance: float,
        confidence: float,
        source_event_seq: int | None,
        state_before: dict[str, Any] | None = None,
        state_after: dict[str, Any] | None = None,
        trigger: str = "",
    ) -> dict[str, Any]:
        scope = "global" if node.type == "director" else ("environment" if str(node.config.get("entity_type", "")) == "environment" else "node")
        return {
            "version": 1,
            "kind": kind,
            "scope": scope,
            "subject": WorkflowEngine._node_subject(node),
            "role": role,
            "importance": max(0.0, min(1.0, importance)),
            "confidence": max(0.0, min(1.0, confidence)),
            "source_event_seq": source_event_seq,
            "state_before": state_before or {},
            "state_after": state_after or {},
            "trigger": trigger,
        }

    @staticmethod
    def _select_memories_for_node(
        memories: Any,
        node: WorkflowNode,
        limit: int = 12,
        *,
        include_peer_facts: bool = True,
    ) -> list[dict[str, Any]]:
        if not isinstance(memories, list):
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in memories:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "fact"))
            scope = str(item.get("scope", "node"))
            node_id = str(item.get("node_id", ""))
            importance = float(item.get("importance", 0.5) or 0.5)
            score = WorkflowEngine._memory_recall_score(item, node)
            relevant = (
                scope in {"global", "environment"}
                or node_id == node.id
                or kind == "state"
                or include_peer_facts and kind == "fact" and (importance >= 0.5 or score >= 0.8)
                or kind == "character" and score >= 0.8
            )
            if relevant:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], int(pair[1].get("id", 0) or 0)))
        return [item for _, item in scored[-max(1, min(limit, 40)) :]]

    def _append_memory_record(
        self,
        *,
        db: Session,
        context: dict[str, Any],
        workflow_id: str,
        run_id: str,
        node: WorkflowNode,
        role: str,
        content: str,
        meta: dict[str, Any],
    ) -> None:
        if not content.strip():
            return
        memory = MemoryRecord(
            workflow_id=workflow_id,
            run_id=run_id,
            node_id=node.id,
            role=role,
            content=content.strip()[:4000],
            tags_json=self._serialize(meta),
        )
        db.add(memory)
        db.commit()
        memory_item = {
            "id": memory.id,
            "run_id": run_id,
            "node_id": node.id,
            "role": memory.role,
            "content": memory.content,
            "tags": [],
            "created_at": memory.created_at.isoformat() if memory.created_at else "",
            **meta,
        }
        current_memory = context.get("_memory", [])
        if not isinstance(current_memory, list):
            current_memory = []
        current_memory.append(memory_item)
        context["_memory"] = current_memory[-100:]

    @staticmethod
    def _extract_role(node_type: str, node: WorkflowNode, output: dict[str, Any]) -> str:
        if node_type == "agent":
            return str(node.config.get("role", "agent"))
        if node_type == "director":
            return "director"
        if node_type == "external_agent":
            return str(node.config.get("agent_id", "external_agent"))
        if node_type == "human_checkpoint":
            return "human"
        return str(output.get("role", "")).strip()

    @staticmethod
    def _pull_latest_director_override(
        db: Session,
        *,
        workflow_id: str,
        run_id: str,
        after_id: int,
    ) -> tuple[int, str | None]:
        rows = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.workflow_id == workflow_id,
                MemoryRecord.run_id == run_id,
                MemoryRecord.role == "director_override",
                MemoryRecord.id > after_id,
            )
            .order_by(MemoryRecord.id.asc())
            .all()
        )
        if not rows:
            return after_id, None
        latest = rows[-1]
        return int(latest.id), str(latest.content).strip() or None

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
        run.output_json = self._serialize(output)
        run.error_json = "null"
        run.ended_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        self._emit_event(
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

    @staticmethod
    def _build_context_snapshot(
        *,
        workflow_id: str,
        node: WorkflowNode,
        runtime_input: dict[str, Any],
        active_incoming_edges: list[WorkflowEdge],
        global_guidance: str,
    ) -> dict[str, Any]:
        interactions = runtime_input.get("_interactions", [])
        simulation = runtime_input.get("_simulation", {})
        simulation_phase = ""
        if isinstance(simulation, dict):
            phases = simulation.get("phases", [])
            idx = int(simulation.get("current_phase_index", 0) or 0)
            if isinstance(phases, list) and 0 <= idx < len(phases) and isinstance(phases[idx], dict):
                simulation_phase = str(phases[idx].get("name", ""))
        return {
            "workflow_id": workflow_id,
            "node_id": node.id,
            "node_type": node.type,
            "upstream_nodes": [edge.source for edge in active_incoming_edges],
            "interaction_count": len(interactions) if isinstance(interactions, list) else 0,
            "has_memory": bool(runtime_input.get("_memory")),
            "has_global_guidance": bool(global_guidance),
            "simulation_phase": simulation_phase,
            "world_state": simulation.get("world_state", {}) if isinstance(simulation, dict) and isinstance(simulation.get("world_state", {}), dict) else {},
            "context_entries": [
                str(entry.get("id", ""))
                for entry in runtime_input.get("_context_pack", {}).get("entries", [])
                if isinstance(entry, dict)
            ]
            if isinstance(runtime_input.get("_context_pack", {}), dict)
            else [],
        }

    @staticmethod
    def _runtime_context_pack_snapshot(runtime_input: dict[str, Any]) -> dict[str, Any]:
        pack = runtime_input.get("_context_pack", {})
        if not isinstance(pack, dict):
            return {"context_entries": []}
        entries = pack.get("entries", [])
        return {
            "episode": pack.get("episode", 0),
            "context_entries": [
                {
                    "id": str(entry.get("id", "")),
                    "title": str(entry.get("title", "")),
                    "kind": str(entry.get("kind", "background")),
                    "activation_reasons": entry.get("activation_reasons", []),
                }
                for entry in entries
                if isinstance(entry, dict)
            ]
            if isinstance(entries, list)
            else [],
        }

    @staticmethod
    def _continuous_snapshot(
        context: dict[str, Any],
        actions: list[dict[str, Any]],
        round_index: int,
        action_index: int,
        simulation_seq: int,
    ) -> dict[str, Any]:
        return {
            "simulation_state": context.get("_simulation", {}),
            "actions": actions,
            "round": round_index,
            "action_index": action_index,
            "simulation_seq": simulation_seq,
        }

    @staticmethod
    def _restore_continuous_checkpoint(context: dict[str, Any], request: RunRequest) -> None:
        rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
        snapshot = rollback.get("continuous_snapshot") if isinstance(rollback, dict) else None
        if not isinstance(snapshot, dict):
            return
        simulation_state = snapshot.get("simulation_state")
        if isinstance(simulation_state, dict):
            context["_simulation"] = simulation_state
        context["_continuous_resume"] = {
            "actions": snapshot.get("actions", []) if isinstance(snapshot.get("actions"), list) else [],
            "round": int(snapshot.get("round", 0) or 0),
            "action_index": int(snapshot.get("action_index", 0) or 0),
            "simulation_seq": int(snapshot.get("simulation_seq", 0) or 0),
        }

    @staticmethod
    def _parse_preflight_confirm_policy(workflow: WorkflowDefinition) -> dict[str, Any]:
        simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
        raw = simulation.get("preflight_confirm", {})
        if not isinstance(raw, dict):
            raw = {}
        enabled = bool(raw.get("enabled", False))
        warmup_nodes = max(0, int(raw.get("warmup_nodes", 1) or 1))
        min_remaining_expensive_nodes = max(1, int(raw.get("min_remaining_expensive_nodes", 1) or 1))
        expensive_node_types = raw.get("expensive_node_types", ["agent", "external_agent", "director"])
        if not isinstance(expensive_node_types, list):
            expensive_node_types = ["agent", "external_agent", "director"]
        expensive_node_types = [str(t) for t in expensive_node_types if str(t).strip()]
        question = str(raw.get("question", "请确认当前方向是否正确，再继续高成本执行。")).strip()
        return {
            "enabled": enabled,
            "warmup_nodes": warmup_nodes,
            "min_remaining_expensive_nodes": min_remaining_expensive_nodes,
            "expensive_node_types": expensive_node_types,
            "question": question,
        }

    @staticmethod
    def _parse_director_quality_policy(workflow: WorkflowDefinition) -> dict[str, Any]:
        simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
        raw = simulation.get("director_quality_control", {})
        if not isinstance(raw, dict):
            raw = {}
        mode = str(simulation.get("mode", "")).strip().lower()
        enabled = bool(raw.get("enabled", True))
        interval_nodes = max(1, int(raw.get("interval_nodes", 2) or 2))
        threshold = float(raw.get("threshold", 0.45 if mode == "roleplay" else 0.4) or 0.4)
        return {
            "enabled": enabled,
            "interval_nodes": interval_nodes,
            "threshold": max(0.0, min(1.0, threshold)),
            "mode": mode,
        }

    @staticmethod
    def _quality_assessment(*, node: WorkflowNode, runtime_input: dict[str, Any], output: dict[str, Any], simulation: dict[str, Any]) -> dict[str, Any]:
        text = output_text(output)
        lowered = text.lower()
        mode = str(simulation.get("mode", "")).strip().lower()
        granularity = str(simulation.get("detail_granularity", "concise")).strip().lower()
        word_count = len(re.findall(r"\S+", text))
        interaction_count = len(runtime_input.get("_interactions", [])) if isinstance(runtime_input.get("_interactions", []), list) else 0

        score = 0.0
        reasons: list[str] = []
        if word_count >= 60:
            score += 0.25
        elif word_count >= 25:
            score += 0.15
        else:
            reasons.append("output_too_short")
        if any(token in lowered for token in ["because", "therefore", "however", "risk", "evidence", "assumption", "原因", "因此", "但是", "风险", "证据", "假设"]):
            score += 0.2
        if any(token in lowered for token in ["decide", "conflict", "change", "pressure", "tension", "turning", "决定", "冲突", "变化", "压力", "紧张", "转折"]):
            score += 0.2
        if any(ch in text for ch in ['"', "'", "“", "”", "：", ":"]):
            score += 0.1
        if interaction_count > 0:
            score += 0.1
        if node.type in {"agent", "external_agent"} and str(node.config.get("entity_type", "")):
            score += 0.05
        if mode == "roleplay" or granularity == "detailed":
            concrete_signals = ["said", "looked", "felt", "moved", "asked", "answered", "说", "看", "感到", "走", "问", "回答"]
            if any(token in lowered for token in concrete_signals):
                score += 0.15
            else:
                reasons.append("lacks_concrete_scene_action")
        if mode == "research":
            research_signals = ["finding", "evidence", "confidence", "limitation", "recommend", "发现", "证据", "置信", "局限", "建议"]
            if any(token in lowered for token in research_signals):
                score += 0.15
            else:
                reasons.append("lacks_research_structure")
        if word_count < 25 and not reasons:
            reasons.append("low_substance")
        return {"score": round(max(0.0, min(1.0, score)), 4), "reasons": reasons, "word_count": word_count}

    @staticmethod
    def _build_quality_guidance(*, node: WorkflowNode, assessment: dict[str, Any], simulation: dict[str, Any]) -> str:
        mode = str(simulation.get("mode", "")).strip().lower()
        reasons = ", ".join(str(r) for r in assessment.get("reasons", [])) or "quality_below_threshold"
        if mode == "roleplay":
            return (
                f"[Director Quality Correction] Node {node.id} output is too flat ({reasons}). "
                "Continue the simulation with concrete scene actions, specific dialogue, visible emotional pressure, "
                "and a clear change in the relationship or situation. Do not summarize when the user expects a vivid scene."
            )
        if mode == "research":
            return (
                f"[Director Quality Correction] Node {node.id} output lacks enough research value ({reasons}). "
                "Continue with explicit assumptions, evidence signals, uncertainty, and decision-useful implications."
            )
        return (
            f"[Director Quality Correction] Node {node.id} output quality is below target ({reasons}). "
            "Continue with more specific actions, clearer causality, and a concrete next state."
        )

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
        assessment = self._quality_assessment(node=node, runtime_input=runtime_input, output=output, simulation=simulation)
        threshold = float(policy.get("threshold", 0.4) or 0.4)
        if float(assessment.get("score", 0.0) or 0.0) >= threshold:
            return last_event_hash, ""
        guidance = self._build_quality_guidance(node=node, assessment=assessment, simulation=simulation)
        _, last_event_hash = self._emit_event(
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

    @staticmethod
    def _build_preflight_preview(
        *,
        workflow: WorkflowDefinition,
        node_id: str,
        node_output: dict[str, dict[str, Any]],
        context: dict[str, Any],
        expensive_remaining: int,
    ) -> dict[str, Any]:
        recent: list[dict[str, Any]] = []
        for k in list(node_output.keys())[-3:]:
            out = node_output.get(k, {})
            snippet = ""
            if isinstance(out, dict):
                snippet = str(out.get("content") or out.get("text") or out.get("summary") or "")[:260]
            recent.append({"node_id": k, "snippet": snippet})
        simulation_state = context.get("_simulation", {}) if isinstance(context.get("_simulation", {}), dict) else {}
        return {
            "workflow_id": workflow.id,
            "next_node_id": node_id,
            "recent_outputs": recent,
            "simulation_state": simulation_state,
            "expensive_nodes_remaining": expensive_remaining,
        }

    @staticmethod
    def _vote_label_from_text(text: str) -> str:
        low = text.lower()
        yes_words = ["yes", "approve", "support", "pass", "accept", "赞成", "同意", "通过", "支持", "可以", "够色"]
        no_words = ["no", "reject", "oppose", "deny", "fail", "反对", "否决", "不通过", "不支持", "不行", "不够色"]
        abstain_words = ["abstain", "neutral", "skip", "hold", "待定", "中立", "弃权", "观望"]
        if any(w in low for w in no_words):
            return "no"
        if any(w in low for w in yes_words):
            return "yes"
        if any(w in low for w in abstain_words):
            return "abstain"
        return "unknown"

    @staticmethod
    def _collect_vote_ballots(interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ballots: list[dict[str, Any]] = []
        for item in interactions:
            source = str(item.get("source", "agent")).strip() or "agent"
            message = str(item.get("message", "")).strip()
            payload = item.get("payload", {})
            label = "unknown"
            if isinstance(payload, dict):
                for key in ["vote", "option", "decision", "result"]:
                    raw = payload.get(key)
                    if isinstance(raw, bool):
                        label = "yes" if raw else "no"
                        break
                    if raw is not None:
                        label = WorkflowEngine._vote_label_from_text(str(raw))
                        if label != "unknown":
                            break
            if label == "unknown":
                label = WorkflowEngine._vote_label_from_text(message)
            ballots.append({"source": source, "vote": label, "reason": message[:300]})
        return ballots

    @staticmethod
    def _is_critical_decision(runtime_input: dict[str, Any], output: dict[str, Any]) -> bool:
        text_parts: list[str] = []
        for v in [runtime_input.get("prompt"), runtime_input.get("question"), output.get("content"), output.get("text"), output.get("summary")]:
            if isinstance(v, str):
                text_parts.append(v.lower())
        all_text = " ".join(text_parts)
        critical_tokens = [
            "是否",
            "决策",
            "方案",
            "go/no-go",
            "approve",
            "reject",
            "关键",
            "重大",
            "推进",
            "立项",
            "剧情",
            "分支",
            "尺度",
            "够色",
        ]
        return any(t in all_text for t in critical_tokens)

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
        if not self._is_critical_decision(runtime_input, output):
            return last_event_hash, ""
        ballots = self._collect_vote_ballots(interactions)
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
        started_seq, last_event_hash = self._emit_event(
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
            cast_seq, last_event_hash = self._emit_event(
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
        _, last_event_hash = self._emit_event(
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
                    _, last_event_hash = self._emit_event(
                        db,
                        run.id,
                        "state_coder",
                        "dynamic_state_updated",
                        {
                            "round": round_index,
                            **expiry_result,
                            "dynamic_state": compact_dynamic_state(expired_state),
                            "_rollback_snapshot": self._continuous_snapshot(
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
                ),
            )
            activation = parse_activation(
                str(activation_response.get("content", "")),
                entities,
                fallback_index=activation_cursor,
            )
            activation_cursor += 1
            active_ids = set(activation["active_node_ids"])
            active_nodes = [node for node in entities if node.id in active_ids]
            _, last_event_hash = self._emit_event(
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
                        output={"actions": recent_actions, "simulation": context.get("_simulation", {})},
                        last_event_hash=load_last_event_hash(db, run.id)[1],
                    )
                director_override_cursor, override_guidance = self._pull_latest_director_override(
                    db,
                    workflow_id=workflow.id,
                    run_id=run.id,
                    after_id=director_override_cursor,
                )
                if override_guidance:
                    guidance = override_guidance
                    context["_global_guidance"] = guidance
                memories = self._select_memories_for_node(
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
                        "actor": self._node_subject(node),
                        "participation": output.get("participation", "act"),
                        "action": output.get("action", ""),
                        "intent": output.get("intent", ""),
                        "observation": output.get("observation", ""),
                        "shared_effect_claim": output.get("shared_effect_claim", ""),
                    }
                )
                if str(output.get("episode_signal") or output.get("boundary_signal", "none")) == "ready":
                    boundary_votes += 1
                context["_simulation"] = update_distributed_state(
                    context.get("_simulation", {}),
                    node=node,
                    output=output,
                    seq=simulation_seq,
                )
                memory_content = str(output.get("action", "")).strip()
                if memory_content:
                    role_name = self._extract_role(node.type, node, output)
                    self._append_memory_record(
                        db=db,
                        context=context,
                        workflow_id=workflow.id,
                        run_id=run.id,
                        node=node,
                        role=role_name,
                        content=memory_content,
                        meta=self._memory_meta(
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
                        "subject": self._node_subject(node),
                        "role": role_name,
                        "last_action": memory_content[:1000],
                        "round": round_index,
                        "action_index": action_index,
                        "self_state": str(output.get("self_update") or output.get("private_state_update", ""))[:1600],
                        "active_intent": str(output.get("intent") or output.get("proposal_summary", ""))[:1200],
                    }
                    self._append_memory_record(
                        db=db,
                        context=context,
                        workflow_id=workflow.id,
                        run_id=run.id,
                        node=node,
                        role=role_name,
                        content=json.dumps(character_state, ensure_ascii=False, default=str),
                        meta=self._memory_meta(
                            kind="character",
                            node=node,
                            role=role_name,
                            importance=0.65,
                            confidence=0.7,
                            source_event_seq=success_seq,
                            state_after=character_state,
                            trigger=f"simulation_action#{action_index}",
                        ),
                    )

            if self._stop_requested(db, run.id):
                return self._finish_stopped_run(
                    db,
                    run,
                    output={"actions": recent_actions, "simulation": context.get("_simulation", {})},
                    last_event_hash=load_last_event_hash(db, run.id)[1],
                )
            should_audit = bool(active_nodes) and (
                boundary_votes > 0
                or round_index % policy.director_interval_rounds == 0
                or action_index >= policy.max_actions
                or round_index >= policy.max_rounds
            )
            if director is not None and should_audit:
                simulation_state = context.get("_simulation", {})
                state_coder_active = isinstance(simulation_state, dict) and dynamic_state_enabled(simulation_state)
                uncoded_actions = recent_actions[state_coder_cursor:] if state_coder_active else []
                state_coder_prompt = state_coder_audit_clause(uncoded_actions) if uncoded_actions else ""
                control_prompt = (
                    "Audit the boundary of this distributed process episode. Nodes own decisions and actions; do not "
                    "invent their actions, conclusions, plot, or research findings. Decide whether the current episode "
                    "should continue, transition to a new macro episode, or stop. This protocol applies to simulation, "
                    "research, collaboration, and roleplay. Return JSON only: "
                    '{"decision":"continue|transition|stop","guidance":"short boundary constraint only",'
                    '"reason":"brief audit reason","shared_state_summary":"compact natural-language public state",'
                    '"state_patch":{"schema_ops":[],"state_ops":[]}}. '
                    "The shared state summary may confirm only outcomes supported by participant outputs or existing state; "
                    "keep unsupported effect claims explicitly uncertain. "
                    f"Do not stop before {policy.min_actions_before_stop} actions unless continuation is impossible. "
                    f"Hard limit is {policy.max_actions}.\n\n"
                    f"{state_coder_prompt}"
                    f"Current macro state:\n{json.dumps(context.get('_simulation', {}), ensure_ascii=False, default=str)}\n\n"
                    f"Autonomous node outputs:\n{json.dumps(recent_actions[-12:], ensure_ascii=False, default=str)}"
                )
                response = await self.provider.chat(
                    model="",
                    system_prompt="You are a continuity auditor and macro-boundary controller, not the central decision maker.",
                    user_prompt=control_prompt,
                )
                control = parse_director_control(str(response.get("content", "")), default_guidance=guidance)
                if control["guidance"]:
                    guidance = control["guidance"]
                    context["_global_guidance"] = guidance
                if control["shared_state_summary"]:
                    context["_simulation"]["shared_context"] = control["shared_state_summary"][:4000]
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
                    _, last_event_hash = self._emit_event(
                        db,
                        run.id,
                        "state_coder",
                        "dynamic_state_updated",
                        {
                            "round": round_index,
                            **coder_result,
                            "dynamic_state": compact_dynamic_state(next_dynamic_state),
                            "_rollback_snapshot": self._continuous_snapshot(
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
                _, last_event_hash = self._emit_event(
                    db,
                    run.id,
                    "director_console",
                    "episode_audited",
                    {"round": round_index, **audit_payload},
                    caused_by="simulation_director_control",
                    prev_hash=last_event_hash,
                )
                if control["decision"] == "transition":
                    context["_simulation"]["episode_index"] = int(context["_simulation"].get("episode_index", 0) or 0) + 1
                    context["_simulation"]["episode_summary"] = control["shared_state_summary"][:4000]
                    phases = context["_simulation"].get("phases", [])
                    current_phase = int(context["_simulation"].get("current_phase_index", 0) or 0)
                    if isinstance(phases, list) and phases:
                        context["_simulation"]["current_phase_index"] = min(len(phases) - 1, current_phase + 1)
                    context["_context_pack"] = build_scene_context_pack(
                        environment=workflow.environment,
                        state=context.get("_simulation", {}),
                        recent_actions=recent_actions,
                    )
                    _, last_event_hash = self._emit_event(
                        db,
                        run.id,
                        "context_engine",
                        "context_pack_activated",
                        activation_trace(context["_context_pack"]),
                        caused_by="episode_transition",
                        context_snapshot=activation_trace(context["_context_pack"]),
                        prev_hash=last_event_hash,
                    )
                stop_requested = control["decision"] == "stop" and action_index >= policy.min_actions_before_stop
            if action_index >= policy.max_actions or stop_requested:
                break

        context["simulation_actions"] = recent_actions
        if self._stop_requested(db, run.id):
            return self._finish_stopped_run(
                db,
                run,
                output={"actions": recent_actions, "simulation": context.get("_simulation", {})},
                last_event_hash=load_last_event_hash(db, run.id)[1],
            )
        final_output: dict[str, Any] = {"content": "\n".join(str(item["action"]) for item in recent_actions)}
        if summary is not None:
            summary_input = {
                **resolve_inputs(summary, context),
                "prompt": (
                    f"{str(summary.inputs.get('prompt', 'Produce the final result.'))}\n\n"
                    "The following are the actual simulation actions. Synthesize them without inventing additional actions "
                    "that did not occur. Preserve the requested output format and language.\n"
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
        run.output_json = self._serialize(
            {"actions": recent_actions, "final": final_output, "simulation": context.get("_simulation", {})}
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
            input_json=self._serialize(self._redact_secrets(runtime_input)),
            output_json="{}",
            error_json="null",
        )
        db.add(node_run)
        db.commit()
        _, last_event_hash = self._emit_event(
            db,
            run_id,
            node.id,
            "queued",
            {"input": runtime_input},
            caused_by=caused_by,
            context_snapshot=self._runtime_context_pack_snapshot(runtime_input),
            prev_hash=last_event_hash,
        )
        _, last_event_hash = self._emit_event(
            db,
            run_id,
            node.id,
            "running",
            {"input": runtime_input},
            caused_by=caused_by,
            context_snapshot=self._runtime_context_pack_snapshot(runtime_input),
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
            node_run.error_json = self._serialize(self._redact_secrets(error))
            db.add(node_run)
            db.commit()
            self._emit_event(
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
            update_distributed_state(
                context.get("_simulation", {}),
                node=node,
                output=output,
                seq=simulation_seq,
            )
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
        node_run.output_json = self._serialize(self._redact_secrets(output))
        db.add(node_run)
        db.commit()
        success_seq, last_event_hash = self._emit_event(
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
        self._validate(workflow)

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
                input_json=self._serialize(request.input),
                output_json="{}",
                error_json="null",
                retry_from_run_id=request.retry_from_run_id,
                retry_from_node=request.retry_from_node,
            )
        else:
            run.status = "running"
            run.workflow_id = workflow.id
            run.input_json = self._serialize(request.input)
            run.output_json = "{}"
            run.error_json = "null"
            run.retry_from_run_id = request.retry_from_run_id
            run.retry_from_node = request.retry_from_node
        db.add(run)
        db.commit()

        node_map = {n.id: n for n in workflow.nodes}
        topo = self._topological_sort(workflow.nodes, workflow.edges)
        incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
        outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in workflow.edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)

        context: dict[str, Any] = {"input": request.input}
        context["_run"] = {"run_id": run_id, "workflow_id": workflow.id}
        context["_environment"] = workflow.environment.model_dump()
        context["_runtime_environment"] = environment_for_runtime(workflow.environment)
        context["_memory"] = self._memories_at_rollback(
            self._load_recent_memories(db, workflow_id=workflow.id, limit=20), request
        )
        context["_simulation"] = init_simulation_state(workflow)
        context["_context_pack"] = build_scene_context_pack(
            environment=workflow.environment,
            state=context["_simulation"],
            recent_actions=[],
        )
        context["_preflight_confirm_policy"] = self._parse_preflight_confirm_policy(workflow)
        context["_director_quality_policy"] = self._parse_director_quality_policy(workflow)
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
            _, last_event_hash = self._emit_event(
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
            self._restore_continuous_checkpoint(context, request)
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
                run.error_json = self._serialize({"message": str(exc)})
                run.ended_at = datetime.now(timezone.utc)
                db.add(run)
                db.commit()
                return run_id
        human_resume_emitted = False
        pending_human_at_start = context.get("_human_response")
        if isinstance(pending_human_at_start, dict):
            resumed_node_id = str(pending_human_at_start.get("node_id", request.retry_from_node or "human")).strip() or "human"
            if self._is_director_preflight_response(pending_human_at_start):
                context["_preflight_confirmed"] = True
            last_event_hash = self._emit_human_resumed(
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
                director_override_cursor, override_guidance = self._pull_latest_director_override(
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
                            if self._should_follow_condition(edge, node_output.get(edge.source, {})):
                                allowed_parent = True
                                active_incoming_edges.append(edge)
                        else:
                            allowed_parent = True
                            active_incoming_edges.append(edge)
                    if not allowed_parent:
                        node_status[node_id] = "skipped"
                        _, last_event_hash = self._emit_event(
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
                        _, last_event_hash = self._emit_event(
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
                    incoming_interactions.append(self._build_interaction_message(edge=edge, source_output=src_out))
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
                    runtime_input = {**runtime_input, "_memory": self._select_memories_for_node(context.get("_memory", []), node)}
                if isinstance(context.get("_simulation"), dict):
                    runtime_input = {**runtime_input, "_simulation": context.get("_simulation", {})}
                if context.get("_global_guidance"):
                    runtime_input = {**runtime_input, "_global_guidance": context.get("_global_guidance", "")}
                pending_human = context.get("_human_response")
                if isinstance(pending_human, dict) and str(pending_human.get("node_id", "")) == node_id:
                    runtime_input = {**runtime_input, "_human_response": pending_human}
                    if self._is_director_preflight_response(pending_human):
                        context["_preflight_confirmed"] = True
                    if not human_resume_emitted:
                        last_event_hash = self._emit_human_resumed(
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
                        preview = self._build_preflight_preview(
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
                        _, last_event_hash = self._emit_event(
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
                        run.error_json = self._serialize(
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
                    input_json=self._serialize(self._redact_secrets(runtime_input)),
                    output_json="{}",
                    error_json="null",
                )
                db.add(node_run)
                db.commit()

                parent_event_ids = [node_success_event_seq[e.source] for e in active_incoming_edges if e.source in node_success_event_seq]
                context_snapshot = self._build_context_snapshot(
                    workflow_id=workflow.id,
                    node=node,
                    runtime_input=runtime_input,
                    active_incoming_edges=active_incoming_edges,
                    global_guidance=str(context.get("_global_guidance", "")),
                )
                _, last_event_hash = self._emit_event(
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
                _, last_event_hash = self._emit_event(
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
                    node_run.output_json = self._serialize(self._redact_secrets(output))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "succeeded"
                    node_output[node_id] = output
                    context[node_id] = output
                    if node.type == "director":
                        global_guidance = str(output.get("global_guidance", "")).strip()
                        if global_guidance:
                            context["_global_guidance"] = global_guidance
                    previous_state_memory = {}
                    if isinstance(context.get("_simulation"), dict):
                        previous_state_memory = context["_simulation"].get("state_memory", {})
                        if not isinstance(previous_state_memory, dict):
                            previous_state_memory = {}
                    next_simulation_state = update_simulation_state(
                        context.get("_simulation", {}),
                        node=node,
                        runtime_input=runtime_input,
                        output=output,
                        status="succeeded",
                        seq=simulation_seq + 1,
                    )
                    success_seq, last_event_hash = self._emit_event(
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
                    role = self._extract_role(node.type, node, output)
                    importance = self._memory_importance(node.type, "succeeded", output)
                    memory_content = self._extract_memory_content(node.type, output)
                    if self._should_write_memory(
                        kind="fact",
                        node=node,
                        content=memory_content,
                        importance=importance,
                        interactions=incoming_interactions,
                    ):
                        self._append_memory_record(
                            db=db,
                            context=context,
                            workflow_id=workflow.id,
                            run_id=run_id,
                            node=node,
                            role=role,
                            content=memory_content,
                            meta=self._memory_meta(
                                kind="fact",
                                node=node,
                                role=role,
                                importance=importance,
                                confidence=0.75,
                                source_event_seq=success_seq,
                                trigger=f"event#{success_seq}",
                            ),
                        )
                    state_memory = next_simulation_state.get("state_memory", {}) if isinstance(next_simulation_state, dict) else {}
                    state_content = json.dumps(state_memory.get("current_states", state_memory), ensure_ascii=False, default=str) if isinstance(state_memory, dict) else ""
                    if isinstance(state_memory, dict) and self._should_write_memory(
                        kind="state",
                        node=node,
                        content=state_content,
                        importance=0.8,
                        state_before=previous_state_memory,
                        state_after=state_memory,
                        interactions=incoming_interactions,
                    ):
                        self._append_memory_record(
                            db=db,
                            context=context,
                            workflow_id=workflow.id,
                            run_id=run_id,
                            node=node,
                            role="state",
                            content=state_content,
                            meta=self._memory_meta(
                                kind="state",
                                node=node,
                                role="state",
                                importance=0.8,
                                confidence=0.7,
                                source_event_seq=success_seq,
                                state_before=previous_state_memory,
                                state_after=state_memory,
                                trigger=f"event#{success_seq}",
                            ),
                        )
                    if node.type in {"agent", "external_agent"}:
                        character_state = {
                            "subject": self._node_subject(node),
                            "role": role,
                            "last_action": memory_content[:500],
                            "last_observation": json.dumps(runtime_input.get("_interactions", []), ensure_ascii=False, default=str)[:500],
                        }
                        character_content = json.dumps(character_state, ensure_ascii=False, default=str)
                        if self._should_write_memory(
                            kind="character",
                            node=node,
                            content=character_content,
                            importance=max(0.6, importance),
                            state_after=character_state,
                            interactions=incoming_interactions,
                        ):
                            self._append_memory_record(
                                db=db,
                                context=context,
                                workflow_id=workflow.id,
                                run_id=run_id,
                                node=node,
                                role=role,
                                content=character_content,
                                meta=self._memory_meta(
                                    kind="character",
                                    node=node,
                                    role=role,
                                    importance=max(0.6, importance),
                                    confidence=0.65,
                                    source_event_seq=success_seq,
                                    state_after=character_state,
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
                    node_run.output_json = self._serialize(self._redact_secrets(wait_payload))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "waiting_human"
                    _, last_event_hash = self._emit_event(
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
                    run.error_json = self._serialize(
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
                    node_run.error_json = self._serialize(self._redact_secrets(error_payload))
                    db.add(node_run)
                    db.commit()
                    node_status[node_id] = "failed"
                    _, last_event_hash = self._emit_event(
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
                    run.error_json = self._serialize(error_payload)
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
            run.output_json = self._serialize(
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
            run.error_json = self._serialize({"message": str(exc)})
            run.ended_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()
            return run_id
