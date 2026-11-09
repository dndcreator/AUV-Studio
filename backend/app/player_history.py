from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import RunEventRecord, RunRecord


CHECKPOINT_EVENTS = {"succeeded", "dynamic_state_updated", "episode_audited", "nodes_activated",
                     "player_applied", "player_control_changed", "state_restored", "player_checkpoint"}


def intervention_history(db: Session, run_id: str, *, before_seq: int | None = None, limit: int = 30) -> dict:
    if db.get(RunRecord, run_id) is None:
        raise HTTPException(404, "run not found")
    query = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id,
        RunEventRecord.event.in_(["player_requested", "player_control_requested"]))
    if before_seq is not None:
        query = query.filter(RunEventRecord.seq < before_seq)
    requests = query.order_by(RunEventRecord.seq.desc()).limit(limit + 1).all()
    more = len(requests) > limit
    requests = requests[:limit]
    resolutions = {}
    if requests:
        wanted = {row.seq for row in requests}
        events = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id,
            RunEventRecord.seq > requests[-1].seq,
            RunEventRecord.event.in_(["player_applied", "player_rejected", "player_action_started", "player_control_changed", "player_control_rejected"])
        ).order_by(RunEventRecord.seq)
        for event in events.yield_per(100):
            data = json.loads(event.payload_json)
            if data.get("request_seq") in wanted:
                resolutions[data["request_seq"]] = (event, data)
    rows = []
    for request in requests:
        data = json.loads(request.payload_json)
        control = request.event == "player_control_requested"
        event, result = resolutions.get(request.seq, (None, {}))
        status = ("applied" if event and event.event in {"player_applied", "player_control_changed"} else
                  "running" if event and event.event == "player_action_started" else "rejected" if event else "queued")
        source_seq = result.get("outcome_seq", result.get("action_event_seq", event.seq if event else request.seq))
        candidates = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id,
            RunEventRecord.seq < source_seq, RunEventRecord.event.in_(CHECKPOINT_EVENTS)).order_by(RunEventRecord.seq.desc())
        before = next((row.seq for row in candidates.yield_per(10) if isinstance(json.loads(row.payload_json).get("_rollback_snapshot"), dict)), None)
        rows.append({"request_seq": request.seq, "request_id": data["request_id"], "mode": "control" if control else data["mode"],
            "actor_id": data.get("actor_id"), "instruction": data.get("instruction", ""), "control": data.get("control"),
            "timestamp": request.timestamp.isoformat(), "status": status, "code": result.get("code"),
            "event_seq": event.seq if event else request.seq, "before_checkpoint_seq": before,
            "after_checkpoint_seq": event.seq if event and isinstance(result.get("_rollback_snapshot"), dict) else None})
    return {"run_id": run_id, "items": rows, "next_before_seq": requests[-1].seq if more else None}
