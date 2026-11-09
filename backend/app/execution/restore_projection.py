from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..audit import verify_run_event_chain
from ..models import RunEventRecord, RunRecord
from ..schemas import RunRequest
from .checkpoints import continuous_snapshot
from .dynamic_state import compact_dynamic_state
from .events import emit_event
from .perception import public_action


def publish_restored_state(db: Session, run: RunRecord, request: RunRequest, context: dict[str, Any]) -> None:
    rollback = request.input.get("_rollback", {})
    source_run, seq = rollback.get("from_run_id"), rollback.get("event_seq")
    if not source_run or type(seq) is not int:
        return  # Internal fixture snapshots have no cross-run display authority.
    source = db.get(RunRecord, source_run)
    rows = db.query(RunEventRecord).filter(RunEventRecord.run_id == source_run,
        RunEventRecord.seq <= seq).order_by(RunEventRecord.seq).all()
    if not source or source.workflow_id != run.workflow_id or not rows or rows[-1].seq != seq:
        raise ValueError("Rollback source does not belong to this workflow.")
    stored = json.loads(rows[-1].payload_json)
    if stored.get("_rollback_snapshot") != rollback.get("continuous_snapshot") or not verify_run_event_chain(rows)[0]:
        raise ValueError("Rollback snapshot or event provenance is invalid.")
    resume = context.get("_continuous_resume", {})
    actions = resume.get("actions", [])
    emit_event(db, run.id, "run_control", "state_restored", {
        "source_run_id": source_run, "checkpoint_seq": seq,
        "source_event_hash": stored["_trace"]["event_hash"],
        "dynamic_state": compact_dynamic_state(context["_simulation"].get("dynamic_state", {})),
        "public_actions": [public_action(action) for action in actions],
        "player_controls": context.get("_player_controls", {}),
        "_rollback_snapshot": continuous_snapshot(context, actions, resume.get("round", 0),
            resume.get("action_index", 0), resume.get("simulation_seq", 0)),
    }, caused_by="verified_rollback")
