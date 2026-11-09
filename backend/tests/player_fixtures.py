import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import RunRecord, WorkflowRecord
from app.schemas import WorkflowDefinition

SECRET = "Wang killed Li without witnesses."


def workflow():
    return WorkflowDefinition.model_validate({"id": "dungeon", "name": "Dungeon", "nodes": [
        {"id": node, "type": "director" if node == "director" else "agent", "position": {"x": 0, "y": 0},
         "config": {"entity_name": node}} for node in ("director", "wang", "li", "bystander")], "edges": [],
        "environment": {"scenario": "A dungeon with separate rooms.", "simulation": {
            "execution_model": "continuous", "horizon_rounds": 1, "dynamic_state": {"enabled": True}}}})


def outcome_patch():
    schema, values = [], []
    for key, subject, predicate, owner, value in (
        ("li_dead", "li", "alive", "wang", False), ("wang_knows", "wang", "knowledge", "wang", SECRET),
    ):
        schema.append({"op": "upsert", "id": key, "description": "Private established consequence.",
            "scope": "entity", "owner_id": owner, "subject_id": subject, "predicate": predicate,
            "value_type": "scalar" if key == "li_dead" else "text", "visibility": "private",
            "retention": {"mode": "persistent"}, "future_relevance": "Constrains future choices.", "reason": "Explicit player outcome."})
        values.append({"op": "set", "concept_id": key, "value": value, "evidence_kind": "player_outcome",
            "confidence": 1, "reason": "Explicit player outcome."})
    return {"status": "apply", "state_patch": {"schema_ops": schema, "state_ops": values}}


class Coder:
    def __init__(self, output=None):
        self.output = outcome_patch() if output is None else output
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return {"content": json.dumps(self.output), "metrics": {"input_tokens": 20, "output_tokens": 20}}


@pytest.fixture
def setup(tmp_path):
    database = create_engine("sqlite:///" + (tmp_path / "interventions.db").as_posix(), connect_args={"check_same_thread": False})
    Base.metadata.create_all(database)
    factory = sessionmaker(bind=database)
    with factory() as db:
        wf = workflow()
        db.add(WorkflowRecord(id=wf.id, name=wf.name, version=1, definition_json=wf.model_dump_json()))
        db.add(RunRecord(id="run", workflow_id=wf.id, status="running"))
        db.commit()
        yield db, wf, factory
    database.dispose()
