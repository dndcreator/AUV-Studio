"""Read and resume existing signed checkpoints; never synthesize a replacement world."""
import json

from fastapi import HTTPException

from .execution.run_definition import workflow_for_run
from .execution.run_definition import definition_hash
from .execution.world_entities import entity_summary
from .audit import verify_run_event_chain
from .models import RunEventRecord, RunRecord


def latest_world_checkpoint(db, run_id):
    run = db.get(RunRecord, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    try:
        workflow = workflow_for_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    for row in db.query(RunEventRecord).filter_by(run_id=run_id).order_by(RunEventRecord.seq.desc()).yield_per(100):
        snapshot = json.loads(row.payload_json).get("_rollback_snapshot")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("simulation_state"), dict):
            prefix = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.seq <= row.seq).order_by(RunEventRecord.seq).all()
            if not verify_run_event_chain(prefix)[0] or snapshot.get("workflow_hash") != definition_hash(workflow):
                raise HTTPException(409, "Saved world checkpoint or definition integrity failed")
            return run, workflow, row, snapshot
    raise HTTPException(409, "No complete world checkpoint exists yet")


def read_world(db, run_id):
    run, workflow, row, snapshot = latest_world_checkpoint(db, run_id)
    return {"run_id": run.id, "status": run.status, "checkpoint_seq": row.seq, "workflow_version": workflow.version,
            "state": snapshot["simulation_state"],
            "entity_summary": entity_summary(snapshot["simulation_state"].get("dynamic_state", {}), include_private=True)}


def resume_checkpoint_seq(db, run_id):
    run, workflow, row, snapshot = latest_world_checkpoint(db, run_id)
    if snapshot.get("batch_partial"):
        raise HTTPException(409, "Partial batch commit requires explicit rollback or reconciliation")
    if run.status in {"pending", "queued", "running", "stopping", "waiting_human"}:
        raise HTTPException(409, "Wait for a stopped safe boundary before resuming")
    tail = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.seq > row.seq).all()
    nodes = {node.id: node for node in workflow.nodes}
    for event in tail:
        node = nodes.get(event.node_id)
        if event.event == "succeeded" and "action" in json.loads(event.payload_json).get("output", {}):
            raise HTTPException(409, "Accepted actions lack a complete checkpoint; choose an explicit rollback")
        if event.event == "running" and node and (node.type in {"tool", "external_agent"}
            or node.config.get("execution_mode") == "external_agent"):
            raise HTTPException(409, "Interrupted external effect requires explicit reconciliation before retry")
    return row.seq
