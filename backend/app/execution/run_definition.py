"""Restore configuration from its source run, never today's editor."""
import hashlib
import json
from ..models import RunDefinitionRecord, RunRecord
from ..schemas import WorkflowDefinition


def definition_hash(workflow):
    return hashlib.sha256(workflow.model_dump_json().encode()).hexdigest()


def workflow_for_run(db, run_id: str) -> WorkflowDefinition:
    saved = db.get(RunDefinitionRecord, run_id)
    if saved is None:
        raise ValueError("This run has no pinned workflow definition; unsafe restore refused")
    return WorkflowDefinition.model_validate_json(saved.definition_json)


def pin_workflow(db, run_id: str, workflow: WorkflowDefinition, source_run_id: str | None):
    existing = db.get(RunDefinitionRecord, run_id)
    if existing:
        return WorkflowDefinition.model_validate_json(existing.definition_json)
    if source_run_id:
        source = db.get(RunRecord, source_run_id)
        if source is None or source.workflow_id != workflow.id:
            raise ValueError("Invalid source workflow for restore")
        workflow = workflow_for_run(db, source_run_id)
    run = db.get(RunRecord, run_id)
    rollback = json.loads(run.input_json).get("_rollback", {})
    data = workflow.model_dump(mode="json")
    data["_lineage"] = {"parent_run_id": run.retry_from_run_id,
                        "parent_event_seq": rollback.get("event_seq") if isinstance(rollback, dict) else None}
    db.add(RunDefinitionRecord(run_id=run_id, definition_json=json.dumps(data, ensure_ascii=False)))
    db.commit()
    return workflow
