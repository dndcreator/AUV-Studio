from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy.orm import Session

from .execution.checkpoints import continuous_snapshot, record_player_checkpoint
from .execution.dynamic_state import compact_dynamic_state, state_coder_instruction
from .execution.events import emit_event, event_transaction
from .execution.provider import ProviderError
from .execution.simulation_loop import build_loop_policy, is_entity_node
from .execution.simulation_state import init_simulation_state
from .execution.state_coding import apply_state_batch, encode_state_batch, pending_state_actions, proposal_from_response
from .models import RunEventRecord, RunRecord, WorkflowRecord
from .schemas import WorkflowDefinition
from .player_controls import close_controls, control_projection


class PlayerIntervention(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["action", "outcome"] = "outcome"
    instruction: str = Field(min_length=1, max_length=6000)
    actor_id: str | None = Field(default=None, min_length=1, max_length=120)
    base_state_version: StrictInt = Field(ge=1)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    conflict_policy: Literal["reject", "overwrite"] = "reject"

    @model_validator(mode="after")
    def action_needs_actor(self):
        if self.mode == "action" and (not self.actor_id or self.conflict_policy != "reject"):
            raise ValueError("actions require an actor_id and cannot override facts")
        return self


def _payload(row: RunEventRecord) -> dict[str, Any]:
    return json.loads(row.payload_json)


def outcome_facts(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Narrator/report projection only. Never add these omniscient facts to peer history."""
    dynamic = compact_dynamic_state(state.get("dynamic_state", {}), include_private=True)
    return [{"description": schema["description"], "subject_id": schema.get("subject_id", ""),
             "value": dynamic["values"][key]["value"], "visibility": schema["visibility"],
             "known_by": schema.get("owner_id", "") if schema["visibility"] == "private" else "public"}
            for key, schema in dynamic["concepts"].items()
            if dynamic["values"].get(key, {}).get("evidence_kind") == "player_outcome"]


def _pending(db: Session, run_id: str) -> RunEventRecord | None:
    request = (db.query(RunEventRecord).filter_by(run_id=run_id, event="player_requested")
               .order_by(RunEventRecord.seq.desc()).first())
    if request is None:
        return None
    resolved = db.query(RunEventRecord).filter(
        RunEventRecord.run_id == run_id, RunEventRecord.seq > request.seq,
        RunEventRecord.event.in_(["player_applied", "player_rejected"])).all()
    return None if any(_payload(row).get("request_seq") == request.seq for row in resolved) else request


def _workflow(db: Session, run: RunRecord) -> WorkflowDefinition:
    record = db.get(WorkflowRecord, run.workflow_id)
    if record is None:
        raise HTTPException(404, "workflow not found")
    return WorkflowDefinition.model_validate_json(record.definition_json)


def intervention_status(db: Session, run_id: str) -> dict[str, Any]:
    run = db.get(RunRecord, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    workflow = _workflow(db, run)
    dynamic = init_simulation_state(workflow)["dynamic_state"]
    latest = (db.query(RunEventRecord).filter_by(run_id=run_id, event="dynamic_state_updated")
              .order_by(RunEventRecord.seq.desc()).first())
    if latest:
        dynamic = _payload(latest).get("dynamic_state", dynamic)
    # A new branch has no state event yet: its restored snapshot is authoritative.
    rollback = json.loads(run.input_json).get("_rollback", {})
    if latest is None and isinstance(rollback, dict):
        dynamic = rollback.get("continuous_snapshot", {}).get("simulation_state", {}).get("dynamic_state", dynamic)
    pending = _pending(db, run_id)
    return {"run_id": run_id, "state_version": dynamic.get("version", 1),
            "enabled": bool(dynamic.get("enabled")),
            "accepts_outcomes": run.status == "running" and build_loop_policy(workflow).enabled and bool(dynamic.get("enabled")),
            "accepts_actions": run.status == "running" and build_loop_policy(workflow).enabled,
            "pending_request_id": _payload(pending)["request_id"] if pending else None,
            **control_projection(db, run)}


def enqueue_intervention(db: Session, run_id: str, request: PlayerIntervention) -> dict[str, Any]:
    with event_transaction(db):
        # This queue is the existing event ledger, not another state or timeline store.
        rows = db.query(RunEventRecord).filter_by(run_id=run_id, event="player_requested").all()
        for row in rows:
            previous = _payload(row)
            if previous.get("request_id") == request.request_id:
                if any(previous.get(key) != value for key, value in request.model_dump().items()):
                    raise HTTPException(409, "request_id already used with different content")
                return {"request_id": request.request_id, "request_seq": row.seq, "status": "recorded"}
        status = intervention_status(db, run_id)
        if not status["accepts_outcomes" if request.mode == "outcome" else "accepts_actions"]:
            raise HTTPException(409, "intervention unavailable in this run")
        if request.base_state_version != status["state_version"]:
            raise HTTPException(409, "world state changed; refresh before submitting")
        if status["pending_request_id"]:
            raise HTTPException(409, "an intervention is already pending")
        run = db.get(RunRecord, run_id)
        workflow = _workflow(db, run)
        if request.actor_id and request.actor_id not in {node.id for node in workflow.nodes if is_entity_node(node)}:
            raise HTTPException(422, "actor_id must reference a simulation entity")
        if request.mode == "action" and request.actor_id in status["handoffs"]:
            raise HTTPException(409, "wait for control handoff")
        seq, _ = emit_event(db, run_id, "player", "player_requested", {
            **request.model_dump(), "control": status["controls"].get(request.actor_id, "autonomous"),
        }, caused_by="user", commit=False)
    return {"request_id": request.request_id, "request_seq": seq, "status": "queued"}


def reject_pending_intervention(db: Session, run_id: str, code: str = "run_ended") -> None:
    with event_transaction(db):
        row = _pending(db, run_id)
        if row:
            emit_event(db, run_id, "player", "player_rejected", {
                "request_seq": row.seq, "request_id": _payload(row)["request_id"], "code": code,
            }, caused_by="player_gateway", parent_event_ids=[row.seq], commit=False)
    if code == "run_ended":
        close_controls(db, run_id)


def pending_action(db: Session, run_id: str) -> RunEventRecord | None:
    row = _pending(db, run_id)
    return row if row and _payload(row).get("mode") == "action" else None


def action_request(db: Session, run_id: str, actor_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
    row = pending_action(db, run_id)
    if row is None or _payload(row).get("actor_id") != actor_id:
        return None
    data = _payload(row)
    code = "state_version_conflict" if data["base_state_version"] != context["_simulation"]["dynamic_state"]["version"] else ""
    if data.get("control", "autonomous") != context.get("_player_controls", {}).get(actor_id, "autonomous"):
        code = "control_changed"
    if code:
        reject_pending_intervention(db, run_id, code)
        return None
    return {**data, "request_seq": row.seq}


def _outcome_prompt(request: dict[str, Any], workflow: WorkflowDefinition, state: dict[str, Any]) -> str:
    # Reuse the State Coder's contract, not a second semantic writer.
    contract = state_coder_instruction(state=state, actions=[], round_index=0)
    entities = [{"node_id": node.id, "name": node.config.get("entity_name", node.id)}
                for node in workflow.nodes if is_entity_node(node)]
    return (
        "PLAYER OUTCOME STATE CODER. This is an explicitly authorized creative/world edit, not an actor's attempt. "
        "Do not reject it for feasibility, character wishes, style, or Director preference. Translate ONLY the requested "
        "outcome into the smallest complete state patch. Do not invent additional outcomes. Do not rewrite history. "
        "If interpretation is ambiguous or irreconcilably conflicts with established facts, return status=conflict "
        "and empty operations; never silently retcon. An explicit requested replacement may update CURRENT values, "
        "but not immutable schema/visibility/owner fields. Do not retire unrelated concepts.\n"
        "The following normal State Coder schema applies, with these authoritative-source exceptions: OMIT source_refs "
        "(the gateway binds the real player_outcome event); every set uses evidence_kind=player_outcome. "
        "Private knowledge may be assigned to any exact valid entity ID, not just the selected actor. "
        "Separate what occurred from who knows it. Secret/unwitnessed outcomes, their identity and causes MUST NOT "
        "appear in global descriptions or values; encode them privately for actual knowers. A death not discovered "
        "is not automatically public knowledge. Do not fabricate witnesses or make the raw instruction public. "
        "State must be sufficient for subsequent participants to inherit consequences.\n"
        + ("\nThe player explicitly confirmed overwriting conflicting CURRENT facts. Apply that choice without rewriting "
           "past events or changing immutable schema/privacy fields.\n" if request.get("conflict_policy") == "overwrite" else "")
        + contract + "\nVALID ENTITY REFERENCES:\n" + json.dumps(entities, ensure_ascii=False)
        + "\nPLAYER REQUEST:\n" + json.dumps(request, ensure_ascii=False)
        + '\nReturn JSON: {"status":"apply|conflict","state_patch":{"schema_ops":[],"state_ops":[]}}. '
        "An apply response must include at least one state operation."
    )


def _validate_references(patch: dict[str, Any], dynamic: dict[str, Any], entity_ids: set[str]) -> None:
    if not patch.get("state_ops") or len(patch["state_ops"]) > 48 or len(patch["schema_ops"]) > 24:
        raise ValueError("invalid_outcome_patch")
    for operation in patch["schema_ops"]:
        if not isinstance(operation, dict):
            raise ValueError("invalid_schema_operation")
        if operation.get("op") not in {"upsert", "retire"}:
            raise ValueError("invalid_schema_operation")
        for key in ("owner_id", "subject_id"):
            reference = operation.get(key)
            if reference and reference not in entity_ids:
                raise ValueError("unknown_entity_reference")
        for key, allowed in (("scope", {"global", "entity"}), ("visibility", {"global", "private"}),
                             ("value_type", {"scalar", "text", "list", "object"})):
            if operation.get("op") == "upsert" and operation.get(key) not in allowed:
                raise ValueError("invalid_schema_field")
        if operation.get("op") == "upsert" and (
            operation["scope"] == "global" and operation.get("owner_id") not in (None, "")
            or operation["scope"] == "entity" and not operation.get("owner_id")
        ):
            raise ValueError("invalid_schema_owner")
    # Existing concepts are already validated; never let the model smuggle transport/config writes.
    known = set(dynamic.get("concepts", {})) | {item.get("id") for item in patch["schema_ops"] if isinstance(item, dict)}
    if any(not isinstance(item, dict) or item.get("concept_id") not in known for item in patch["state_ops"]):
        raise ValueError("unknown_concept_reference")


async def drain_intervention(*, db: Session, run: RunRecord, workflow: WorkflowDefinition, provider: Any,
                             context: dict[str, Any], actions: list[dict[str, Any]], round_index: int,
                             action_index: int, simulation_seq: int) -> None:
    """Called only between admitted actions; a player outcome is never an Agent action."""
    row = _pending(db, run.id)
    if row is None:
        return
    from .execution.run_definition import pin_workflow, definition_hash
    pin_workflow(db, run.id, workflow, None)
    context["_workflow_hash"] = definition_hash(workflow)
    request = _payload(row)
    if request.get("mode") == "action":
        return
    state = context["_simulation"]
    metrics: list[dict[str, Any]] = []
    try:
        if request["base_state_version"] != state["dynamic_state"]["version"]:
            raise ValueError("state_version_conflict")
        # Older admitted actions must be coded before a newer authoritative outcome.
        uncoded = pending_state_actions(state["dynamic_state"], actions)
        if uncoded:
            scoped = provider.with_output_limit(2400) if hasattr(provider, "with_output_limit") else provider
            response = await asyncio.wait_for(scoped.chat(model="", system_prompt="You are the State Coder, not a participant.",
                user_prompt=state_coder_instruction(state=state, actions=uncoded, round_index=round_index)), timeout=45)
            metrics.append(response.get("metrics", {}))

            def record_coding(next_state, result):
                next_context = {**context, "_simulation": {**context["_simulation"], "dynamic_state": next_state}}
                emit_event(db, run.id, "state_coder", "dynamic_state_updated", {
                    **result, "dynamic_state": compact_dynamic_state(next_state),
                    "_rollback_snapshot": continuous_snapshot(next_context, actions, round_index - 1, action_index, simulation_seq),
                }, caused_by="pre_intervention_coding")
                context["_simulation"] = next_context["_simulation"]

            await asyncio.wait_for(encode_state_batch(provider=scoped, state=state, proposal=proposal_from_response(response),
                actions=uncoded, seq=simulation_seq, round_index=round_index, record=record_coding), timeout=45)
            state = context["_simulation"]
            if request["base_state_version"] != state["dynamic_state"]["version"]:
                raise ValueError("state_version_conflict")
        prompt = _outcome_prompt(request, workflow, state)
        if len(prompt) > 90000:
            raise ValueError("outcome_context_too_large")
        scoped = provider.with_output_limit(2400) if hasattr(provider, "with_output_limit") else provider
        for attempt in (1, 2):
            response = await asyncio.wait_for(scoped.chat(model="", system_prompt="You are the PLAYER OUTCOME STATE CODER.",
                                                          user_prompt=prompt), timeout=45)
            metrics.append(response.get("metrics", {}))
            try:
                # The existing parser rejects duplicate keys, nonfinite numbers and truncation.
                patch = proposal_from_response(response, director_envelope=True)
                envelope = json.loads(str(response.get("content", "")).strip().removeprefix("```json").removesuffix("```").strip())
                if patch.get("parse_error"):
                    raise ValueError("invalid_outcome_protocol")
                if envelope.get("status") == "conflict":
                    raise ValueError("outcome_conflict")
                if envelope.get("status") != "apply":
                    raise ValueError("invalid_outcome_protocol")
                entity_ids = {node.id for node in workflow.nodes if is_entity_node(node)}
                _validate_references(patch, state["dynamic_state"], entity_ids)
                # Validate once with a placeholder source; bind the actual event only in the transaction.
                _bind_source(patch, row.seq)
                _, result = apply_state_batch(state["dynamic_state"], patch, actions=[], seq=simulation_seq,
                                               round_index=round_index, source_outcome_seq=row.seq)
                if result["rejected"] or not result["changed"]:
                    raise ValueError("outcome_patch_rejected")
                break
            except (ValueError, TypeError, AttributeError) as exc:
                if str(exc) == "outcome_conflict" or attempt == 2:
                    raise ValueError("outcome_conflict" if str(exc) == "outcome_conflict" else "outcome_patch_rejected") from exc
                prompt += "\nPrevious output was invalid. Repair schema/types/entity references against the SAME request and state."
        with event_transaction(db):
            db.refresh(run)
            if run.status != "running":
                raise ValueError("run_ended")
            record_player_checkpoint(db, run.id, {**request, "request_seq": row.seq}, context, actions,
                                     round_index, action_index, simulation_seq, commit=False)
            outcome_seq, _ = emit_event(db, run.id, "player", "player_outcome", {
                "request_seq": row.seq, "request_id": request["request_id"], "instruction": request["instruction"],
                "actor_id": request.get("actor_id"), "base_state_version": request["base_state_version"],
            }, caused_by="user", parent_event_ids=[row.seq], commit=False)
            _bind_source(patch, outcome_seq)
            next_state, result = apply_state_batch(state["dynamic_state"], patch, actions=[], seq=simulation_seq,
                                                   round_index=round_index, source_outcome_seq=outcome_seq)
            if result["rejected"]:
                raise ValueError("outcome_patch_rejected")
            next_context = {**context, "_simulation": {**state, "dynamic_state": next_state}}
            emit_event(db, run.id, "state_coder", "dynamic_state_updated", {
                **result, "dynamic_state": compact_dynamic_state(next_state), "request_id": request["request_id"],
                "_rollback_snapshot": continuous_snapshot(next_context, actions, round_index - 1, action_index, simulation_seq),
            }, caused_by="player_outcome", parent_event_ids=[outcome_seq], commit=False)
            emit_event(db, run.id, "player", "player_applied", {
                "request_seq": row.seq, "request_id": request["request_id"], "state_version": next_state["version"],
                "outcome_seq": outcome_seq, "model_metrics": metrics,
                "_rollback_snapshot": continuous_snapshot(next_context, actions, round_index - 1, action_index, simulation_seq),
            }, caused_by="player_gateway", parent_event_ids=[outcome_seq], commit=False)
        context["_simulation"] = next_context["_simulation"]
    except (ValueError, ProviderError, TimeoutError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "state_version_conflict", "outcome_conflict", "outcome_context_too_large", "run_ended", "outcome_patch_rejected",
        } else "outcome_model_failed"
        emit_event(db, run.id, "player", "player_rejected", {
            "request_seq": row.seq, "request_id": request["request_id"], "code": code, "model_metrics": metrics,
        }, caused_by="player_gateway", parent_event_ids=[row.seq])


def _bind_source(patch: dict[str, Any], event_seq: int) -> None:
    for operation in patch["schema_ops"] + patch["state_ops"]:
        operation["source_refs"] = [{"event_type": "player_outcome", "event_seq": event_seq}]
