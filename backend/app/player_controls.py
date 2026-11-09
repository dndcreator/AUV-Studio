from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .execution.checkpoints import continuous_snapshot, record_player_checkpoint
from .execution.events import emit_event, event_transaction
from .execution.simulation_loop import build_loop_policy, is_entity_node
from .models import RunEventRecord, RunRecord, WorkflowRecord
from .schemas import WorkflowDefinition


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor_id: str = Field(min_length=1, max_length=120)
    control: Literal["autonomous", "player_controlled"]
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


def control_projection(db: Session, run: RunRecord) -> dict[str, Any]:
    snapshot = json.loads(run.input_json).get("_rollback", {}).get("continuous_snapshot", {})
    controls = dict(snapshot.get("player_controls", {}))
    waiting: dict[str, bool] = {}
    pending = {}
    for row in db.query(RunEventRecord).filter(RunEventRecord.run_id == run.id,
        RunEventRecord.event.in_(["player_control_requested", "player_control_changed", "player_control_rejected", "player_waiting"])
    ).order_by(RunEventRecord.seq):
        data = json.loads(row.payload_json)
        actor = data.get("actor_id", "")
        if row.event == "player_control_requested":
            pending[actor] = {**data, "request_seq": row.seq}
        elif row.event == "player_control_changed":
            controls[actor] = data["control"]
            pending.pop(actor, None)
        elif row.event == "player_control_rejected":
            pending.pop(actor, None)
        else:
            waiting[actor] = bool(data.get("waiting"))
    return {"controls": controls,
            "handoffs": {actor: data["control"] for actor, data in pending.items()},
            "waiting_actor_ids": [actor for actor, active in waiting.items() if active]}


def request_control(db: Session, run_id: str, request: ControlRequest) -> dict[str, Any]:
    with event_transaction(db):
        run = db.get(RunRecord, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        record = db.get(WorkflowRecord, run.workflow_id)
        if record is None:
            raise HTTPException(404, "workflow not found")
        workflow = WorkflowDefinition.model_validate_json(record.definition_json)
        existing = db.query(RunEventRecord).filter_by(run_id=run_id, event="player_control_requested").all()
        for row in existing:
            data = json.loads(row.payload_json)
            if data.get("request_id") == request.request_id:
                if any(data.get(key) != value for key, value in request.model_dump().items()):
                    raise HTTPException(409, "request_id already used with different content")
                return {"request_id": request.request_id, "status": "recorded"}
        if run.status != "running" or not build_loop_policy(workflow).enabled:
            raise HTTPException(409, "control requires a running continuous simulation")
        if request.actor_id not in {node.id for node in workflow.nodes if is_entity_node(node)}:
            raise HTTPException(422, "actor_id must reference a simulation entity")
        if request.actor_id in control_projection(db, run)["handoffs"]:
            raise HTTPException(409, "control handoff already pending")
        seq, _ = emit_event(db, run_id, "player", "player_control_requested", request.model_dump(),
                            caused_by="user", commit=False)
    return {"request_id": request.request_id, "request_seq": seq, "status": "handoff"}


def apply_controls(db: Session, run: RunRecord, context: dict[str, Any], actions: list[dict[str, Any]],
                   round_index: int, action_index: int, simulation_seq: int) -> None:
    cursor = context.get("_player_control_cursor", 0)
    rows = db.query(RunEventRecord).filter(RunEventRecord.run_id == run.id,
        RunEventRecord.event == "player_control_requested", RunEventRecord.seq > cursor).order_by(RunEventRecord.seq).all()
    for row in rows:
        data = json.loads(row.payload_json)
        with event_transaction(db):
            db.refresh(run)
            if run.status != "running":
                return
            record_player_checkpoint(db, run.id, {**data, "request_seq": row.seq}, context, actions,
                                     round_index, action_index, simulation_seq, commit=False)
            controls = {**context.get("_player_controls", {}), data["actor_id"]: data["control"]}
            next_context = {**context, "_player_controls": controls, "_player_control_cursor": row.seq}
            emit_event(db, run.id, "player", "player_control_changed", {
                **{key: data[key] for key in ("actor_id", "control", "request_id")}, "request_seq": row.seq,
                "_rollback_snapshot": continuous_snapshot(next_context, actions, round_index - 1, action_index, simulation_seq),
            }, caused_by="player_handoff", parent_event_ids=[row.seq], commit=False)
        context.update({"_player_controls": controls, "_player_control_cursor": row.seq})


def close_controls(db: Session, run_id: str) -> None:
    run = db.get(RunRecord, run_id)
    if not run:
        return
    with event_transaction(db):
        state = control_projection(db, run)
        requests = db.query(RunEventRecord).filter_by(run_id=run_id, event="player_control_requested").all() if state["handoffs"] else []
        sources = {json.loads(row.payload_json).get("actor_id"): row for row in requests}
        for actor, desired in state["handoffs"].items():
            source = sources[actor]
            data = json.loads(source.payload_json)
            emit_event(db, run_id, "player", "player_control_rejected", {
                "actor_id": actor, "control": desired, "code": "run_ended",
                "request_seq": source.seq, "request_id": data["request_id"],
            }, caused_by="player_handoff", commit=False)
        for actor in state["waiting_actor_ids"]:
            emit_event(db, run_id, actor, "player_waiting", {"actor_id": actor, "waiting": False}, commit=False)
