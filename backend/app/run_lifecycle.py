from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .audit import load_last_event_hash
from .execution.events import emit_event
from .models import NodeRunRecord, RunRecord


ACTIVE_STATUSES = ("pending", "queued", "running", "stopping")


def fail_interrupted_run(db: Session, run_id: str, code: str, message: str) -> bool:
    db.rollback()
    run = db.get(RunRecord, run_id)
    if run is None or run.status not in ACTIVE_STATUSES:
        return False
    error = {"code": code, "message": message}
    now = datetime.now(timezone.utc)
    run.status = "failed"
    run.error_json = json.dumps(error)
    run.ended_at = now
    for node in db.query(NodeRunRecord).filter(
        NodeRunRecord.run_id == run_id, NodeRunRecord.status.in_(["pending", "running", "queued"]),
    ):
        node.status = "failed"
        node.error_json = json.dumps(error)
        node.ended_at = now
        if node.started_at is not None:
            start = node.started_at if node.started_at.tzinfo else node.started_at.replace(tzinfo=timezone.utc)
            node.duration_ms = max(0, int((now - start).total_seconds() * 1000))
    try:
        # Commit the status transition and its signed event together.
        emit_event(db, run_id, "run_control", "failed", {"error": error},
                   caused_by="recovery" if code == "RUNTIME_INTERRUPTED" else "queue",
                   prev_hash=load_last_event_hash(db, run_id)[1])
    except Exception:
        db.rollback()
        raise
    return True


def recover_interrupted_runs(db: Session) -> int:
    """Call only after acquiring the database's exclusive runtime lease."""
    recovered = 0
    while True:
        rows = db.query(RunRecord.id).filter(RunRecord.status.in_(ACTIVE_STATUSES)).limit(100).all()
        if not rows:
            return recovered
        for row in rows:
            recovered += int(fail_interrupted_run(
                db, row.id, "RUNTIME_INTERRUPTED",
                "The previous service exited before this run finished. Retry or roll back to a saved checkpoint.",
            ))
